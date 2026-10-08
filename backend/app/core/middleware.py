"""Security middleware (D-014, Section 33).

Two ASGI middleware classes, both pure ASGI rather than `BaseHTTPMiddleware`
because they need no request body and no streaming support. BaseHTTPMiddleware
buffers bodies and has a history of subtle bugs around disconnects; there is no
reason to accept either for a header check and a cookie comparison.

`CSRFMiddleware` uses a signed double-submit token. The plain double-submit
pattern compares a cookie against a header, which is only as strong as the
cookie: an attacker who can set a cookie on the domain (a subdomain
takeover, or a sibling application on the same registrable domain) chooses both
halves. Signing the nonce with a server secret means a forged cookie cannot
produce a matching header.
"""

from __future__ import annotations

import hmac
import secrets
import time
from hashlib import sha256
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

REQUEST_ID_HEADER = "X-Request-Id"
# Bound to every request: what §55 asks to be in a structured log, minus the
# identity fields, which only the tenant dependency can resolve.
REQUEST_LOG_FIELDS = ("request_id", "method", "path", "status", "duration_ms")


class RequestContextMiddleware:
    """Bind §55's request-scoped fields for the life of one request.

    `bind_context` existed since Phase 8 and nothing called it, so no log line
    carried a request id and none of them could be correlated with each other —
    the masking processor had nothing to correlate. This is the middleware that
    makes `request_id`, `method`, `path`, `status` and `duration_ms` automatic,
    and the tenant dependency adds `workspace_id` and `user_id` once it has
    resolved them.

    An inbound `X-Request-Id` is honoured so a caller (or an upstream proxy) can
    stitch its own trace to ours; anything that is not a short opaque token is
    replaced rather than echoed, because a log line is not a place to accept a
    caller's string unexamined.
    """

    def __init__(self, app: ASGIApp, max_length: int = 64) -> None:
        self.app = app
        self.max_length = max_length

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        inbound = headers.get(REQUEST_ID_HEADER, "")
        request_id = _safe_request_id(inbound) or uuid4().hex
        path = scope.get("path", "")
        method = scope.get("method", "")

        clear_contextvars()
        bind_contextvars(
            request_id=request_id,
            method=method,
            path=path,
        )
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                raw_headers = MutableHeaders(scope=message)
                raw_headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            # Bound before the log call: binding merges, so without this
            # the last request's status and duration would leak into the next.
            bind_contextvars(status=status_holder["status"], duration_ms=duration_ms)
            logger.info(
                "request_completed",
                status=status_holder["status"],
                duration_ms=duration_ms,
                workspace_id=_bound_workspace(),
            )
            clear_contextvars()


def _safe_request_id(value: str) -> str | None:
    """Accept an opaque token or nothing."""
    candidate = value.strip()
    if not candidate or len(candidate) > 64:
        return None
    if not all(character.isalnum() or character in "-_" for character in candidate):
        return None
    return candidate


def _bound_workspace() -> str | None:
    from structlog.contextvars import get_contextvars

    bound = get_contextvars()
    value = bound.get("workspace_id")
    return None if value is None else str(value)

# Paths that legitimately have no cookie yet. A client that calls one of these
# first can log in without a preflight round trip.
CSRF_EXEMPT_PATHS = frozenset({"/health", "/ready"})


class SecurityHeadersMiddleware:
    """Response headers that are correct by default and cheap to keep that way."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        headers = {
            "x-content-type-options": "nosniff",
            # The command center is not embeddable. Deny rather than
            # same-origin: framing a catalog UI inside an attacker's page is
            # clickjacking.
            "x-frame-options": "DENY",
            "referrer-policy": "no-referrer",
            # Authenticated responses must never sit in a shared cache.
            "cache-control": "no-store",
            "cross-origin-opener-policy": "same-origin",
        }
        if settings.is_production:
            # Only meaningful over TLS, and only safe to send from production.
            headers["strict-transport-security"] = (
                "max-age=31536000; includeSubDomains"
            )
        self.headers = headers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                for key, value in self.headers.items():
                    mutable[key] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class CSRFTokens:
    """Mint and check signed double-submit tokens.

    Separated from the middleware so the `/auth/csrf` endpoint can hand back a
    token the caller can actually use. Two implementations of the same signing
    scheme would drift, and the failure mode of that drift is a 403 on every
    write with nothing in the logs to explain it.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.secret = settings.jwt_secret.encode()
        self.cookie_name = settings.csrf_cookie_name
        self.header_name = settings.csrf_header_name

    def issue(self) -> str:
        """`nonce.hmac`. The nonce is random per cookie; the signature binds it.

        Signing is what makes this more than a plain double submit: a token the
        server did not mint can never compare equal to a header value, even if
        an attacker managed to set the cookie.
        """
        nonce = secrets.token_urlsafe(32)
        digest = hmac.new(self.secret, nonce.encode(), sha256).hexdigest()
        return f"{nonce}.{digest}"

    def valid(self, value: str | None) -> bool:
        if not value or "." not in value:
            return False
        nonce, _, _digest = value.rpartition(".")
        return hmac.compare_digest(self.issue_for(nonce), value)

    def issue_for(self, nonce: str) -> str:
        digest = hmac.new(self.secret, nonce.encode(), sha256).hexdigest()
        return f"{nonce}.{digest}"

    def matches(self, cookie_value: str | None, header_value: str | None) -> bool:
        """Both halves present, cookie genuine, and the two identical."""
        return bool(
            cookie_value
            and header_value
            and self.valid(cookie_value)
            and hmac.compare_digest(cookie_value, header_value)
        )

    def cookie_header(self, value: str) -> str:
        return (
            f"{self.cookie_name}={value}; Path=/; "
            f"SameSite={self.settings.session_cookie_samesite}"
            f"{'; Secure' if self.settings.cookie_secure else ''}"
        )


class CSRFMiddleware:
    """Reject state-changing API calls that cannot prove they are same-origin."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.tokens = CSRFTokens(settings)
        self.api_prefix = settings.api_prefix

    def _cookie_header(self, value: str) -> str:
        return self.tokens.cookie_header(value)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        path: str = scope.get("path", "")
        method: str = scope.get("method", "GET").upper()
        cookie_token = _cookie_value(
            headers.get("cookie"), self.tokens.cookie_name
        )

        if method in SAFE_METHODS or path in CSRF_EXEMPT_PATHS:
            # Hand out a token on any safe request so a fresh client can
            # authenticate itself before its first POST.
            if not self.tokens.valid(cookie_token):
                token = self.tokens.issue()
                # Put the cookie into the request scope as well as the response.
                # Without this, a handler that reads `request.cookies` sees no
                # token and mints a second one, so the client ends up with two
                # Set-Cookie headers and whichever arrives last silently
                # invalidates the other.
                _inject_cookie(scope, self.tokens.cookie_name, token)
                await self._respond_with_cookie(scope, receive, send, token)
                return
            await self.app(scope, receive, send)
            return

        if not path.startswith(self.api_prefix):
            await self.app(scope, receive, send)
            return

        header_token = headers.get(self.tokens.header_name)
        if not self.tokens.matches(cookie_token, header_token):
            await self._reject(send)
            return

        await self.app(scope, receive, send)

    async def _respond_with_cookie(
        self, scope: Scope, receive: Receive, send: Send, token: str
    ) -> None:
        """Serve the request, adding the CSRF cookie to the response."""
        sent = False

        async def send_with_cookie(message: Message) -> None:
            nonlocal sent
            if message["type"] == "http.response.start" and not sent:
                sent = True
                mutable = MutableHeaders(scope=message)
                mutable.append("set-cookie", self._cookie_header(token))
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    async def _reject(self, send: Send) -> None:
        body = (
            b'{"detail":"CSRF token missing or invalid. '
            b"GET a URL first to obtain one, then send it in the "
            b'X-CSRF-Token header."}'
        )
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _inject_cookie(scope: Scope, name: str, value: str) -> None:
    """Add `name=value` to the request's Cookie header, in place.

    Rewrites the scope header list rather than the response, so every downstream
    component reads the same cookie the client will send back.
    """
    headers = MutableHeaders(scope=scope)
    existing = headers.get("cookie")
    if existing:
        headers["cookie"] = f"{existing}; {name}={value}"
    else:
        headers["cookie"] = f"{name}={value}"


def _cookie_value(raw: str | None, name: str) -> str | None:
    if not raw:
        return None
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None
